"""The disclosure register — structured records, not model output.

WHAT THIS MODULE IS FOR
-----------------------
In a regulated sales conversation, some sentences are not stylistic choices. A
mandatory disclosure either happened or it did not, the requirement varies by
jurisdiction, and the answer has to survive an audit years later. So the product
keeps a **register**: a per-jurisdiction table of required disclosure codes, each
with the registered phrasings that satisfy it, and a per-session ledger of which
codes were actually satisfied and by which utterance.

The register is deterministic code over a data table. No model is asked whether
a disclosure "basically happened". That separation is the point of this file, and
it is what makes the compliance half of this domain *testable*: the register is
the ground truth a stochastic scorer's compliance claims can be checked against.

WHY THIS IS THE INTERESTING SEAM
--------------------------------
A scoring model that decides for itself whether the risk warning was given has
two failure modes with the same symptom — it can be wrong about the words, and it
can be wrong about the requirement — and neither is separable from the other in
the output. Once the requirement lives in a table, the only question left for the
model is a question about the transcript, and the answer can be diffed against
the ledger. `roleplay.contracts.ScoreClaimContract` is that diff.

WHY A LIVE TRAINEE CHANGES NOTHING HERE
---------------------------------------
`roleplay.live` can put a real model in the trainee's chair. That model produces
fluent, plausible, *unregistered* sentences about risk all day long, and the
temptation is to loosen the matcher so the good ones get credit. Loosening it is
the one change this file must not accept: the register is the instrument the
scorer's compliance claims are measured against, and an instrument that credits
"there is some risk, of course" has no way to catch a scorer that credits it too.

What the live path adds instead is the other side of the comparison —
`keyword_shadow_codes`, the lax check a reasonable engineer writes in an
afternoon, kept here on purpose so the gap between the two is a *number* rather
than an anecdote. Same discipline as the naive control in `lab.voice.calibration`,
which exists to be wrong by a measured margin. A live session where the shadow
credits three disclosures and the register records none is the finding.

`compliance_brief` is the counterweight that keeps the strictness honest. A firm
that requires exact wording issues that wording to its advisers, so the exemplary
competence level is briefed with it and the weaker levels are not. Without that,
every live session would score zero on disclosure, the criterion would have no
spread, and "the register is strict" would be indistinguishable from "the register
is broken".

WHAT IS DELIBERATELY CRUDE
--------------------------
Matching is substring-over-normalised-text against a closed list of registered
phrasings. That is much stricter than a real register would be, and it is chosen
on purpose: a strict register produces *false negatives* (a trainee who conveyed
the warning in unregistered words gets no credit), and a false negative in the
ground truth is a visible, arguable gap. A loose register would produce false
positives, and a ground truth that over-credits cannot be used to catch a scorer
that over-credits. When the instrument and the system under test share a bias,
the measurement is worth nothing.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Sequence

__all__ = [
    "DISCLOSURE_CODES",
    "JURISDICTIONS",
    "REGISTERED_PHRASINGS",
    "KEYWORD_SHADOW_TERMS",
    "DisclosureRecord",
    "DisclosureRegister",
    "ShadowComparison",
    "approved_wording",
    "compliance_brief",
    "keyword_shadow_codes",
    "compare_with_keyword_check",
    "normalise",
    "required_codes",
]


#: Every disclosure code this product knows about, with the one line that says
#: what it is. A closed vocabulary, for the same reason the scenario corpus has
#: one: a code that is not described here cannot be required by a jurisdiction,
#: so "required a disclosure that does not exist" is an error at import time
#: rather than a requirement nothing can ever satisfy.
DISCLOSURE_CODES: dict[str, str] = {
    "capital_at_risk": "the customer can get back less than they put in",
    "past_performance": "past returns do not predict future returns",
    "fees_and_charges": "the ongoing cost of holding the product is stated",
    "product_suitability": "the recommendation rests on a completed suitability review",
    "conflict_of_interest": "the adviser's own remuneration on this sale is declared",
}

#: Which codes each jurisdiction requires. Generic labels: this is a demonstration
#: pack, and the point being made is that the requirement set is *data keyed by
#: market*, not that these are the real rule numbers of any real regulator.
JURISDICTIONS: dict[str, tuple[str, ...]] = {
    "eu-retail": ("capital_at_risk", "past_performance", "fees_and_charges"),
    "apac-retail": (
        "capital_at_risk",
        "past_performance",
        "fees_and_charges",
        "product_suitability",
    ),
    "amer-retail": ("capital_at_risk", "fees_and_charges", "conflict_of_interest"),
}

#: The registered phrasings, per language, per code. A disclosure is recorded
#: when a trainee utterance contains one of these, normalised.
#:
#: Two languages, because a register keyed only by jurisdiction and not by
#: language is the bug this pack's locale rows exist to find: the requirement is
#: the same in Madrid and Manchester, and the words are not.
REGISTERED_PHRASINGS: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        "capital_at_risk": (
            "capital at risk",
            "you could get back less than you put in",
            "the value of your investment can fall",
            "you may get back less than you invest",
        ),
        "past_performance": (
            "past performance is not a guide to future performance",
            "past performance is no guarantee of future returns",
            "what it did last year tells you nothing about next year",
        ),
        "fees_and_charges": (
            "annual management charge",
            "ongoing charges figure",
            "the total cost of holding it is",
        ),
        "product_suitability": (
            "once we have completed the suitability assessment",
            "we would need to complete a suitability review first",
        ),
        "conflict_of_interest": (
            "we receive a commission on this product",
            "the bank is paid a fee when you buy this",
        ),
    },
    "es": {
        "capital_at_risk": (
            "puede recuperar menos de lo que invierte",
            "el valor de su inversion puede bajar",
        ),
        "past_performance": (
            "la rentabilidad pasada no garantiza la rentabilidad futura",
        ),
        "fees_and_charges": (
            "la comision de gestion anual",
            "el coste total de mantenerlo es",
        ),
        "product_suitability": (
            "tendriamos que completar primero el test de idoneidad",
        ),
        "conflict_of_interest": (
            "el banco recibe una comision cuando usted compra este producto",
        ),
    },
}

#: ------------------------------------------------------------------- CONTROL
#: The naive instrument, kept beside the real one so the difference can be
#: measured rather than asserted. These are the words a keyword check looks for
#: when someone is asked to "check the risk warning was given" and has an
#: afternoon to do it. It is wrong in both directions and both directions matter:
#:
#:   over-credits  "there is no real risk to your capital here" contains `risk`
#:                 and `capital`, and satisfies nothing at all;
#:   under-credits the list is English, so a correctly disclosed Spanish session
#:                 scores zero.
#:
#: Nothing in the product consults this table. It exists to be compared against,
#: and `compare_with_keyword_check` is the comparison.
KEYWORD_SHADOW_TERMS: dict[str, tuple[str, ...]] = {
    "capital_at_risk": ("risk", "capital", "value can go", "ups and downs"),
    "past_performance": ("past performance", "last year", "track record", "historic"),
    "fees_and_charges": ("fee", "charge", "cost", "per cent", "%"),
    "product_suitability": ("suitab", "assessment", "review"),
    "conflict_of_interest": ("commission", "we are paid", "we get paid", "incentive"),
}

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]")


def normalise(text: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace.

    Accent stripping is what lets the Spanish phrasings above be written without
    diacritics and still match a trainee who types them correctly — and, more
    importantly, still match a trainee who does not. A register that recorded a
    disclosure only when the accents were right would be measuring a keyboard.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", stripped.casefold())).strip()


def required_codes(jurisdiction: str) -> tuple[str, ...]:
    """The disclosure codes `jurisdiction` requires.

    Raises on an unknown jurisdiction rather than returning an empty tuple. An
    empty requirement set is indistinguishable from "fully compliant", and a
    typo in a market code must not read as a clean session.
    """
    try:
        return JURISDICTIONS[jurisdiction]
    except KeyError:
        raise KeyError(
            f"unknown jurisdiction {jurisdiction!r}; known: {sorted(JURISDICTIONS)}"
        ) from None


@dataclass(frozen=True)
class DisclosureRecord:
    """One satisfied requirement: which code, by which words, on which turn."""

    code: str
    jurisdiction: str
    language: str
    turn: int
    phrasing: str
    utterance: str

    def describe(self) -> str:
        return (
            f"{self.code} satisfied on trainee turn {self.turn} "
            f"by the registered phrasing {self.phrasing!r}"
        )


@dataclass
class DisclosureRegister:
    """The per-session ledger of satisfied requirements.

    Stateful, and scoped to one session by construction — the register is created
    inside the session and dies with it, which is why it cannot be the source of
    the cross-session leak seeded in `roleplay.scorer`.
    """

    jurisdiction: str = "eu-retail"
    language: str = "en"
    records: list[DisclosureRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        required_codes(self.jurisdiction)  # validate now, not at the first read
        if self.language not in REGISTERED_PHRASINGS:
            raise KeyError(
                f"no registered phrasings for language {self.language!r}; "
                f"known: {sorted(REGISTERED_PHRASINGS)}"
            )

    # ------------------------------------------------------------- recording

    def observe(self, utterance: str, *, turn: int) -> list[DisclosureRecord]:
        """Record every required code this utterance satisfies for the first time.

        Returns the records created by *this* utterance, so the caller can emit
        one `record_disclosure` tool event per record and the trace shows which
        sentence discharged which requirement.
        """
        haystack = normalise(utterance)
        phrasings = REGISTERED_PHRASINGS[self.language]
        created: list[DisclosureRecord] = []
        for code in required_codes(self.jurisdiction):
            if self.satisfied(code):
                continue
            for phrasing in phrasings.get(code, ()):
                if normalise(phrasing) in haystack:
                    record = DisclosureRecord(
                        code=code,
                        jurisdiction=self.jurisdiction,
                        language=self.language,
                        turn=turn,
                        phrasing=phrasing,
                        utterance=utterance,
                    )
                    self.records.append(record)
                    created.append(record)
                    break
        return created

    # ---------------------------------------------------------------- reading

    def satisfied(self, code: str) -> bool:
        return any(record.code == code for record in self.records)

    def satisfied_codes(self) -> tuple[str, ...]:
        """Satisfied codes in requirement order, so two sessions compare cleanly."""
        return tuple(c for c in required_codes(self.jurisdiction) if self.satisfied(c))

    def missing_codes(self) -> tuple[str, ...]:
        return tuple(c for c in required_codes(self.jurisdiction) if not self.satisfied(c))

    @property
    def complete(self) -> bool:
        """True when every code this jurisdiction requires has a record."""
        return not self.missing_codes()

    def summary(self) -> str:
        """One line with a numerator and a denominator, never a bare verdict."""
        required = required_codes(self.jurisdiction)
        return (
            f"{len(self.satisfied_codes())}/{len(required)} required disclosures "
            f"recorded for {self.jurisdiction} in {self.language}"
            + (f"; missing: {', '.join(self.missing_codes())}" if self.missing_codes() else "")
        )


# --------------------------------------------------------------------------- #
# The approved wording — what a firm actually gives its advisers
# --------------------------------------------------------------------------- #


def approved_wording(jurisdiction: str, language: str = "en") -> dict[str, str]:
    """The one registered phrasing per required code, in requirement order.

    The register holds several accepted phrasings per code; a firm's compliance
    handbook holds one. This returns the first, which is the canonical one, so a
    trainee briefed from it and a register reading its transcript are working from
    the same table. Deriving the brief from `REGISTERED_PHRASINGS` rather than
    restating it is the whole point: a brief that drifted from the register would
    train an adviser to say a sentence the register no longer accepts, and the
    resulting failure would look like a model problem.

    Raises on an unknown jurisdiction or language, for the reason `required_codes`
    does: a silently empty brief is indistinguishable from a compliant one.
    """
    try:
        phrasings = REGISTERED_PHRASINGS[language]
    except KeyError:
        raise KeyError(
            f"no registered phrasings for language {language!r}; "
            f"known: {sorted(REGISTERED_PHRASINGS)}"
        ) from None
    wording: dict[str, str] = {}
    for code in required_codes(jurisdiction):
        options = phrasings.get(code, ())
        if not options:
            raise KeyError(
                f"{jurisdiction} requires {code!r} but {language!r} registers no "
                "phrasing for it, so the requirement cannot be discharged in this "
                "language and no brief should pretend otherwise"
            )
        wording[code] = options[0]
    return wording


def compliance_brief(jurisdiction: str, language: str = "en") -> str:
    """The approved wording as briefing text for a trainee prompt.

    Only the exemplary competence level is given this (see
    `roleplay.live.TRAINEE_BRIEFS`). Handing it to every level would make the
    disclosure criterion a copying exercise and destroy the spread the scorer is
    supposed to be graded on; handing it to none of them would make a perfect
    score unreachable and turn a strict instrument into a broken one.
    """
    lines = [
        f"Your firm's approved disclosure wording for {jurisdiction} "
        f"({language}). Use these sentences as written — compliance records them "
        "verbatim and a paraphrase is not recorded:",
    ]
    for code, phrasing in approved_wording(jurisdiction, language).items():
        lines.append(f'  - {code}: "{phrasing}"')
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The naive control
# --------------------------------------------------------------------------- #


def keyword_shadow_codes(
    utterances: Sequence[str], *, jurisdiction: str
) -> tuple[str, ...]:
    """Which codes a keyword check would credit across these utterances.

    Deliberately lax and deliberately English-only. This is not a fallback for
    the register and must never be wired into one: it is the control arm, and its
    only job is to be wrong in a way the register is not.
    """
    haystack = normalise(" ".join(utterances))
    credited: list[str] = []
    for code in required_codes(jurisdiction):
        terms = KEYWORD_SHADOW_TERMS.get(code, ())
        if any(normalise(term) in haystack for term in terms if term.strip("% ")):
            credited.append(code)
    return tuple(credited)


@dataclass(frozen=True)
class ShadowComparison:
    """Register versus keyword check, per code, with both directions kept.

    `over_credited` is the interesting column — a code the keyword check would
    have passed and the register did not record. Every entry in it is a sentence
    that sounds like a disclosure and is not one, which is exactly the failure a
    compliance report must not contain.

    `under_credited` is the honest other half: a code the register recorded and a
    keyword check would have missed. It is not evidence that the register is
    generous, it is evidence that vocabulary matching is not a subset of anything.
    """

    jurisdiction: str
    required: tuple[str, ...]
    recorded: tuple[str, ...]
    keyword_credited: tuple[str, ...]

    @property
    def over_credited(self) -> tuple[str, ...]:
        return tuple(c for c in self.keyword_credited if c not in self.recorded)

    @property
    def under_credited(self) -> tuple[str, ...]:
        return tuple(c for c in self.recorded if c not in self.keyword_credited)

    @property
    def agreed(self) -> tuple[str, ...]:
        return tuple(c for c in self.recorded if c in self.keyword_credited)

    def summary(self) -> str:
        """One line, with both denominators. A bare count here would be a defect."""
        n = len(self.required)
        return (
            f"{self.jurisdiction}: register {len(self.recorded)}/{n}, "
            f"keyword check {len(self.keyword_credited)}/{n}"
            + (
                f"; keyword over-credits {', '.join(self.over_credited)}"
                if self.over_credited
                else "; no over-credit"
            )
            + (
                f"; keyword misses {', '.join(self.under_credited)}"
                if self.under_credited
                else ""
            )
        )


def compare_with_keyword_check(
    register: "DisclosureRegister", utterances: Sequence[str]
) -> ShadowComparison:
    """Score the same transcript both ways and return the disagreement.

    `utterances` must be the trainee's turns only. Passing the customer's turns as
    well would let the customer's own words about risk discharge the adviser's
    obligation, which is the mistake that makes a compliance report worthless.
    """
    return ShadowComparison(
        jurisdiction=register.jurisdiction,
        required=required_codes(register.jurisdiction),
        recorded=register.satisfied_codes(),
        keyword_credited=keyword_shadow_codes(
            utterances, jurisdiction=register.jurisdiction
        ),
    )
