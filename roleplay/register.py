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

__all__ = [
    "DISCLOSURE_CODES",
    "JURISDICTIONS",
    "REGISTERED_PHRASINGS",
    "DisclosureRecord",
    "DisclosureRegister",
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
